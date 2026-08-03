# nyxloom — event-sourced control plane for autonomous coding agents

Second draft of the handoff control plane. Same goals and invariants as
[`nyxloom` draft 1](../nyxloom/README.md) — token-free process
supervision, cheap implementers behind an independent review gate, durable
provenance, typed stop conditions, a zero-AI dashboard. Why:
[REVIEW-OF-DRAFT1.md](REVIEW-OF-DRAFT1.md).

> **Status: accepted 2026-07-15 · implementation in progress**, amended
> 2026-07-15 same day to **the resident daemon from the start** (see Deciding
> log below) and further evolved since — see "Current state" immediately
> below for what is actually true of the running system today; the table and
> narrative further down are the ORIGINAL draft-2 design rationale and are
> historical in the specific places superseded by that amendment and by
> later cutovers (marked inline).

> **Using or adopting nyxloom?** Start with the practical guide —
> [`docs/USAGE.md`](docs/USAGE.md): concepts, onboarding a project's direction
> spine, the full CLI reference, and free-model discovery.

## Current state (machine-checked)

<!-- product-truth:state_backend=sqlite -->
<!-- product-truth:daemon_mode=resident -->
<!-- product-truth:merge_mode=guarded-automatic -->

These three facts are asserted against the real running configuration by
`tests/test_product_truth.py` on every gate run (CR-01, DR-04) — if either
side drifts, that test fails until the docs and the config agree again:

- **State backend: SQLite**, authoritative (`NYXLOOM_STATE_BACKEND=sqlite`,
  `nyxloomd/docker-compose.yml`; live since 2026-07-21). The file-backed
  `events.jsonl`/statefile design in the table below is the ORIGINAL design;
  CR-04 removes the now-unused file backend and its selector entirely.
- **Daemon mode: resident.** `nyxloomd` is a long-lived container service
  (`restart: unless-stopped`), not the stateless cron-driven `nyxloom tick`
  the table below describes — the 2026-07-15 amendment below built the
  daemon from the start; `nyxloom tick --once` survives only as a degraded/
  debug mode.
- **Merge mode: guarded-automatic.** `nyxloom-trove/nyxloom.toml`
  `[policy].merge_mode` — a real `git merge --no-ff` via a scratch worktree,
  escalating to `NEEDS_OPERATOR` on a genuine conflict, never firing while a
  project is paused (shipped, P48). The "no automated merge" framing under
  Non-goals below is the ORIGINAL pre-implementation stance and no longer
  describes the shipped system.

## Deciding log

- 2026-07-15 · **Accepted by user, with one amendment: the daemon from the
  start.** `nyxloomd` is built in the first implementation wave as a resident
  reconciler: it runs the same idempotent reconcile pass on an internal
  interval, disk stays authoritative (restart = rescan + replay; killing the
  daemon loses nothing), attempt wrappers stay detached processes that survive
  daemon restarts, and leases stay flock-based. What residency buys now rather
  than at graduation: immediate exit collection, in-process scheduling, and a
  read-only HTTP/SSE surface serving the rendered dashboard and live log
  tails. `nyxloom tick --once` is retained as the degraded/debug mode.
  ARCHITECTURE §2's cron/timer scheduling and §9's graduation criteria are
  amended accordingly; ROADMAP M2 builds the daemon, M5's daemon item is moot.
- 2026-07-15 · Implementation carved as file-disjoint packages
  (`handoff/P01…P11`) against frontier-written frozen contracts
  (`src/nyxloom/` core + stub docstrings); cheap-tier agents implement,
  frontier reviews. Dogfood rule SPEC §14.6 applies from P01 onward.

## The three inversions

**Storage and Runtime rows below are the ORIGINAL draft-2 design intent, both
since superseded** — see "Current state" above for what actually runs today
(SQLite authoritative; a resident `nyxloomd`, not a stateless tick). Kept for
the design rationale (why draft 2 started by inverting draft 1's choices);
not a current claim.

| | Draft 1 | Draft 2 (original design intent — superseded, see above) | Why |
| --- | --- | --- | --- |
| Storage | SQLite WAL authoritative; md + JSON sidecar handoffs | Markdown handoff with YAML frontmatter is the single source; append-only `events.jsonl` + per-task statefiles; any DB is a rebuildable index | One contract, one file — no drift surface. Humans and AI sessions read state with zero tooling. (Review F1, F4) |
| Runtime | Long-lived `nyxloomd` daemon owning subprocesses and leases | Stateless `nyxloom tick` (cron/timer, 2–5 min) + a ~20-line per-attempt wrapper that captures exit/receipt; flock(2) leases released by the kernel on crash | The system's cadence is minutes-to-hours; a dead tick is a missed tick, not an incident. flock beats both marker files and daemon bookkeeping on one host. (F2, F3) |
| Correctness | Generic spec-sufficiency audit prose | **`nyxloom lint`** — the P51–P85 deciding-log lessons as machine-checked carve rules, with the incident corpus as golden tests | The cost model rests on carve quality; make the lessons executable, not archival. (F5) |

## Components

| Component | Responsibility | Uses AI? |
| --- | --- | --- |
| `nyxloom lint` | Frontmatter schema + carve-quality rules (SPEC §6); gates the carve commit | No |
| `nyxloom tick` | Reconciler: scan → dispatch/detect/collect → events → render → notify → exit | No |
| Attempt wrapper | Runs one CLI leg detached; tees log; writes typed receipt with exit code; holds/releases flock leases | Only the launched agent |
| Route adapters | Per-CLI dispatch/resume/probe/usage-extraction templates, table-driven from `routes.toml` | No |
| `nyxloom render` | Static HTML dashboard from files (tables, DAG, timeline, drill-down, cost) | No |
| `nyxloom notify` / `decide` / `pause` / `doctor` / `status` | Operator surface; typed events; decision loop; emergency brake; drift audit | No |
| Frontier roles | Carve, review pass #2, merge, decision prep — unchanged from workflow v2 | Yes |

The standing LLM controller session (Sonnet low + heartbeats) is **retired** at
milestone M2: every duty in v2 §10 — header parsing, dependency/slot checks,
preflight, dispatch, stall detection, packet assembly, status reporting — is
deterministic and moves into the tick. Frontier tokens keep flowing to exactly
the two places they buy quality: carve and review.

## Documents

- [Architecture](docs/ARCHITECTURE.md) — file layout, tick engine, wrapper,
  leases, routes, cost capture, dashboard, notifications/decision loop,
  daemon graduation criteria. (§1's storage claim is superseded — see
  "Current state" above.)
- [Specification](docs/SPEC.md) — normative states, tick rules, lint rules
  L1–L13, stop policy and progress ratchet, spec-health triggers, self-tests.
- Self-dev roadmap: [`nyxloom-trove/3-roadmap.md`](nyxloom-trove/3-roadmap.md)
  — the adopted, schema-validated, currently-active milestone list
  (`nyxloom.toml`'s `roadmap` key). The original `docs/ROADMAP.md` (M0–M5) is
  archived at
  [`docs/archive/product-docs/ROADMAP.md`](docs/archive/product-docs/ROADMAP.md)
  (superseded, CR-01/DR-04).
- `docs/EVOLUTION.md` (in-place adoption from the pre-nyxloom md workflow) is
  archived at
  [`docs/archive/product-docs/EVOLUTION.md`](docs/archive/product-docs/EVOLUTION.md)
  — the migration it describes is complete (historical, CR-01/DR-04).
- [`schemas/`](schemas/) — handoff frontmatter, statefile, event; example
  `routes.toml`.

## Non-goals (inherited from draft 1, mostly unchanged)

- No claim of semantic correctness — process guarantees only.
- No product decisions without the designated authority.
- No model in the scheduler, poller, renderer, or notifier — ever.
- Claude Remote Control / Channels are never the bus or scheduler; they are an
  optional, user-initiated discussion surface (ARCHITECTURE §8).

Automated merge (originally deferred here pending exact-commit provenance) is
now live as **guarded-automatic** — see "Current state" above; this bullet
list no longer lists it as a non-goal because it shipped (P48).

# nyxloom — files are the database, ticks are the daemon, lint is the guarantee

Second draft of the handoff control plane. Same goals and invariants as
[`nyxloom` draft 1](../nyxloom/README.md) — token-free process
supervision, cheap implementers behind an independent review gate, durable
provenance, typed stop conditions, a zero-AI dashboard — with **storage and
delivery inverted**. Why: [REVIEW-OF-DRAFT1.md](REVIEW-OF-DRAFT1.md).

> **Status: accepted 2026-07-15 · implementation in progress** (`src/`,
> `handoff/` packages). Draft 1's state model, stop outcomes, review
> invariants, and security boundary are inherited (largely verbatim) — this
> draft changes *how the system is stored, built, and adopted*, not what it
> guarantees.

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

| | Draft 1 | Draft 2 | Why |
| --- | --- | --- | --- |
| Storage | SQLite WAL authoritative; md + JSON sidecar handoffs | **Markdown handoff with YAML frontmatter is the single source**; append-only `events.jsonl` + per-task statefiles; any DB is a rebuildable index | One contract, one file — no drift surface. Humans and AI sessions read state with zero tooling. (Review F1, F4) |
| Runtime | Long-lived `nyxloomd` daemon owning subprocesses and leases | **Stateless `nyxloom tick`** (cron/timer, 2–5 min) + a ~20-line per-attempt wrapper that captures exit/receipt; **flock(2) leases** released by the kernel on crash | The system's cadence is minutes-to-hours; a dead tick is a missed tick, not an incident. flock beats both marker files and daemon bookkeeping on one host. (F2, F3) |
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
  daemon graduation criteria.
- [Specification](docs/SPEC.md) — normative states, tick rules, lint rules
  L1–L12, stop policy and progress ratchet, spec-health triggers, self-tests.
- [Roadmap](docs/ROADMAP.md) — M0–M5, each with exit criteria and the token
  saving realized at that milestone.
- [Evolution](docs/EVOLUTION.md) — in-place adoption from the current md
  workflow; per-step rollback; no import, no second store.
- [`schemas/`](schemas/) — handoff frontmatter, statefile, event; example
  `routes.toml`.

## Non-goals (inherited from draft 1, unchanged)

- No claim of semantic correctness — process guarantees only.
- No product decisions without the designated authority.
- No automated merge until exact-commit provenance is demonstrated (separate
  user decision; manual merge may remain permanent policy).
- No model in the scheduler, poller, renderer, or notifier — ever.
- Claude Remote Control / Channels are never the bus or scheduler; they are an
  optional, user-initiated discussion surface (ARCHITECTURE §8).

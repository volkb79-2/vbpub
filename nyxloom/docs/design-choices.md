# nyxloom design choices

Rationale for cross-cutting choices, so a future reader (or agent) sees *why*, not just *what*.
Living doc — append a dated section per decision.

---

## Storage formats: SQLite for state, JSONL for logs, no binary (2026-07-21)

nyxloom has two durable data streams with *opposite* priorities, so they get *different* formats:

| Stream | Priority | Format | Why |
|---|---|---|---|
| **Authoritative state** (event log + task projection) | consistency, atomicity | **SQLite** (`state.db`, per project) | one transaction makes the event append + projection update atomic — eliminates the divergence class the file store guards against with `doctor`. `sqlite3` is stdlib → zero-dep, single portable file. See `plan-state-integrity.md`. |
| **Diagnostic logs** | grep / tail / tooling / human-read | **JSONL** (`logs/nyxloom.jsonl`) | append-only, streamable, `tail -f`-able, one corrupt line ≠ dead file, and the entire log ecosystem already reads it. See `plan-logging.md`. |

**Why not one format for both?** They pull opposite ways. State wants transactional consistency
(SQLite); logs want to be greppable/tailable/disposable (JSONL). Forcing either into the other's
format loses the property that matters.

**Why not binary (systemd-journal style) for logs?** The journal is binary + indexed + compressed
+ tamper-sealed because it's a *system-wide, high-volume, security-sensitive* log needing indexed
queries — and you can only read it via `journalctl`. At nyxloom's volume the #1 need is "just look
at it", so binary would mean **reinventing journalctl** to read our own logs. Rejected. If indexed
history queries ever become necessary, the graceful upgrade is **DuckDB/SQLite *over* the JSONL**
(a query layer on top) — no change to the write format, no bespoke reader.

**JSON vs JSONL.** A single JSON document must be fully parsed to read and rewritten to append
(one corrupt byte kills the file). JSONL (NDJSON) is one object per line: O(1) append, streaming
read, tail/grep-friendly, corruption-isolated. That append/stream profile is why logs use it.

---

## Consuming JSONL logs — tooling (2026-07-21)

Because logs are JSONL, "big and not human-readable" is a *rendering* problem the ecosystem
already solves — no bespoke viewer needed:

| Tool | Role | Notes |
|---|---|---|
| **`lnav`** | log navigator | **cockpit pick (below).** Auto-detects JSONL, live tail, **SQL over the log**, in/out filters, syntax highlight, timeline histogram, jump-to-context. Single binary. |
| `klp` | table view | pip-installable; JSONL/logfmt → colored table; time + grep filters. Python-native, frictionless where lnav's binary isn't wanted. |
| `visidata` | spreadsheet TUI | opens JSONL as an interactive table; great for ad-hoc aggregation. pip. |
| `jq` | transform/filter | `jq -r '[.ts,.level,.logger,.msg]\|@tsv' \| column -t` → instant table. The shell workhorse. |
| `duckdb` | SQL over files | `SELECT level,count(*) FROM 'logs/*.jsonl' GROUP BY 1` — no import. The "indexed query" escape hatch. |
| `jless` / `fx` | interactive pager | explore one big object/stream. |
| `hl` / humanlog | pretty-printer | JSON log lines → colored human lines. |

Our in-dashboard **Logs page** (`plan-logging.md` P04) is deliberately a browser-native `lnav`:
level filter + highlight + context-around-line + live tail. The CLI tools mean an operator can do
the same from a shell *without* the UI — a property we get *for free* by choosing JSONL.

### Cockpit tool: **`lnav`** (add to the devcontainer)
Best fit for the cockpit's "inspect the running stack" role and a direct answer to "view JSONL
like a nicely readable table-formatted log with filter/highlight/context." Install via
`apt-get install lnav` (or the static binary). Does **not** conflict with the cockpit doctrine
(it's inspection, not a browser engine). Usage: `lnav ~/.local/state/nyxloom/logs/nyxloom.jsonl`
(and the rotated `.zst` segments once compression is on — lnav reads compressed logs directly).
`klp` is the pip-only fallback if a binary install is unwanted.

---

## Log rotation & retention (2026-07-21)

**Keep the last 3 days as native JSONL; zstd-compress older segments** (operator directive).
- **Hot window:** the current day + the previous 2 stay **uncompressed** `.jsonl` — needed for
  append, `tail -f`, the live UI, and instant `lnav`/`grep`.
- **Cold segments:** on the daily roll that ages a file past 3 days, it is compressed to
  `nyxloom.jsonl.<date>.zst` (JSONL's repeated keys compress ~10–20×). `zstd` for speed+ratio.
- **Mechanism:** a daily `TimedRotatingFileHandler` (or a size-guarded time rotator) whose
  `rotator`/`namer` zstd-compress on rollover, applied only once a segment leaves the 3-day native
  window. Retention beyond that = a configurable number of `.zst` backups.
- This is `plan-logging.md`'s D-L6, resolved.

---

## Control-plane authentication: keep the private-bridge trust model (2026-07-21)

nyxloom's HTTP control plane (`POST /api/config/*` — pause/resume, edit policy, answer
decisions) is **unauthenticated**. **Decision (operator, 2026-07-21): keep it as-is, documented.**

- **Trust model:** the daemon binds only to the private, unpublished ciu bridge — no port is
  published to the host or beyond the internal docker network (`http_bind` is infra-sourced via
  `NYXLOOM_HTTP_BIND`, default loopback; the daemon prints a startup warning if bound off-loopback).
  Anything that can reach the control plane is already inside the trusted network boundary.
- **Why not add auth now:** a shared-secret token or a fronting auth proxy is defense-in-depth
  against an *exposure that does not exist* in the current single-tenant private-bridge deployment.
  The cost (token plumbing / proxy) buys nothing until a port is actually published.
- **Revisit trigger:** if the control plane is ever bound to a published/host-reachable interface
  (the startup warning fires), add a shared-secret token on mutating POSTs **before** exposing it.
  This note is the standing record so that decision isn't silently skipped.

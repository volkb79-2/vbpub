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

## Control-plane authentication: keep the private-bridge trust model (2026-07-21) — SUPERSEDED 2026-08-03

> **Superseded by CR-15 (2026-08-03).** The revisit trigger below fired and was then missed
> for eleven days: P38 (2026-07-22) set `NYXLOOM_HTTP_BIND: "0.0.0.0"` in
> `nyxloomd/docker-compose.yml`, so the control plane WAS bound to a network-reachable
> interface — every container on `nyxloomd-net`, the devcontainer included by design, held
> full control authority, and `POST /api/decision/reply` is the mechanism the product's
> central promise ("the human owns direction") runs on. The 2026-08-02 deep-review
> amendment filed that as RISK-005 [P0]; CR-15 implements it: every mutating POST requires
> an operator credential (`src/nyxloom/control_auth.py`, `Authorization: Bearer <secret>`)
> whose named identity becomes the `Actor` of the resulting events, refusals are audited,
> and GETs stay open so the dashboard remains readable on a trusted network.
>
> **The HTTP surface was not the only ingress.** The ntfy feedback topic mutates the same
> invariants — `pause`/`resume` chat-ops, `decide D-NNN <choice>`, and every decision-chat
> turn — and it authenticates nothing: `NTFY_CMD_TOKEN` is the daemon's own *read*
> credential for subscribing, ntfy exposes no sender identity at all, and the actors it
> recorded (`ntfy-cmd`, `feedback-chat`) were transport names, the same non-identity as
> `ui`. Authenticating one door and leaving the other open would have been theatre, so
> CR-15 closes that ingress by default: its mutating routes refuse with a fixed reply, write
> nothing, and audit one refusal, unless the deployment sets
> `NYXLOOM_CHANNEL_OPERATOR_ID` to name the operator its topic's write ACL belongs to.
> That variable is an *assertion by the deployment*, not authentication, and it is
> documented as such — nyxloom cannot verify a topic ACL, it can only record whether the
> claim was made and attribute the resulting events to the named human. Read verbs
> (`help`/`status`/`digest`) stay open, mirroring the HTTP read/write split.
>
> **The standing lesson is about the mechanism, not the verdict:** a "revisit trigger"
> phrased as a condition a human is supposed to notice ("the startup warning fires") is not
> a trigger. It fired into a log nobody read. The record below is kept verbatim as the
> reasoning that was correct for its deployment and wrong for the next one.

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

---

## Doc ownership: nyxloom ships its doctrine; troves carry only project deltas (2026-07-23)

**Problem.** The canonical reference docs (`AUTHORING.md`, `STANDARD.md`, `STANDING.md`)
physically live in `nyxloom-trove/` — nyxloom's *own consumer trove* — and
`onboarding.scaffold_trove` (`src/nyxloom/onboarding.py:243-253`) `copyfile`s them into every
new consumer. So "canonical reference" and "nyxloom's self-consumption copy" are literally the
same files, and each consumer holds an unmanaged duplicate that silently goes stale. Because
nyxloom self-hosts (nyxloom develops nyxloom), this ambiguity is structural, not cosmetic.

**The deciding question is operational, not editorial** (operator, 2026-07-23): *how will people
actually run nyxloom once it ships?* Ownership falls out of the deployment model:

| UC | Scenario | Doctrine source | Consumer's role |
|---|---|---|---|
| **UC1** | nyxloom released as a product, run as a **container on a host** (target) | **baked into the image**; doctrine version == nyxloom release. A doc update ships *as* a nyxloom update. | client root mounted in; trove holds project content only |
| **UC2** | nyxloomd-spawned CLI agents **containerized, folder-limited** (D-R7) | mounted **read-only from the product image** into each agent container | agent also gets its worktree (r/w, scope-limited) + the project's trove from the client mount |
| **UC3** | **direct-drive** by a human / CLI agent (today, transitional) | repo-root `AGENTS.md` points at the product's reference docs | `CLAUDE.md` → thin `@import AGENTS.md`, so Claude Code still loads it |
| **UC4** | **nyxloom develops nyxloom** (self-hosting) | the same product `reference/` | nyxloom's own trove is just another client — **zero reference authority** |

**Decision.** The doctrine is **part of the product**, not of any trove:

- **Canonical `reference/` ships inside the nyxloom package/image, outside every trove** —
  `AUTHORING.md`, `STANDARD.md`, and a new `DOCTRINE.md` (operational lessons).

  **Correction, made while implementing this (2026-07-23):** `STANDING.md` is **not** canonical.
  Its content is nyxloom's own wave/frozen-file contract — project-specific, which is exactly why
  it was never stamped to any consumer. Promoting it would have pushed nyxloom's frozen-file list
  onto every client. It stays in nyxloom's own trove as a project delta; the *mechanism* (a trove
  may carry a `STANDING.md` inherited by every handoff there) is documented in canonical
  `STANDARD.md`. This is the sibling-overlay model working as designed: canonical defines the
  mechanism, the trove carries the project's actual contract. Also note `controller-workflow-v2.md`
  is already nyxloom-owned (`nyxloom/legacy-workflow-origin/`), and no
  `reasonix-controller-guide.md` exists in either repo — it was referenced but never written.
- **Names and locations are spec constants** (this doc + `SPEC.md`), never discovered at runtime.
  *Why not an env var / lookup verb:* discovery only pays off when a location is unknown or
  deployment-variable. Here the product defines both the reference dir and the trove name
  (`nyxloom-trove`) by spec, so an env var only adds a failure mode (unset / wrong / divergent)
  and buys nothing. Bind-mount today vs baked image tomorrow changes the *host* path, never the
  product-relative path an agent uses.
- **Two layers via same-named siblings — no extra directory.** Each canonical `reference/X.md`
  instructs its reader: *also check `nyxloom-trove/X.md` for project-specific additions or
  overrides.* Canonical = general; trove sibling = project delta. A dedicated `local/` /
  `add-ons/` folder was rejected: the shared filename already expresses the pairing, and one
  convention beats two. **Precedent already in the tree:** canonical `STANDARD.md` documents the
  optional `nyxloom-trove/GUIDE.md` ("`AGENTS.md` carries a one-line pointer… the detail lives in
  the trove"), first adopted by dstdns 2026-07-16 — this decision generalizes that single
  catch-all doc into a per-document overlay.
- **No copies.** Consumers stop carrying stamped duplicates of canonical docs, so drift becomes
  structurally impossible rather than lint-policed.
- **Onboarding stamps an informational `README.md`** into each new trove: what the trove owns,
  that canonical doctrine lives upstream in the product, and how the sibling-override convention
  works.

**Migration ledger (measured 2026-07-23, before any change).** "Nothing gets lost" was *verified*,
not assumed — every stamped copy was diffed against canonical:

| Consumer | `AUTHORING.md` | `STANDARD.md` | `STANDING.md` | consumer-unique |
|---|---|---|---|---|
| dstdns | identical | **stale** (44 canonical-only lines) | absent | `GUIDE.md` (216 ln) |
| topos | absent | **stale** | absent | — |
| netcup-api-filter | identical | identical | absent | `STATE.md` |
| nyxloom (self) | canonical source | canonical source | canonical source | — |

**Decisive result: 0 consumer-only lines in any stamped copy** — all drift is canonical-newer.
No project-specific content is trapped in the duplicates, so deleting them is lossless. What must
be preserved is the genuinely consumer-authored material: dstdns's `GUIDE.md` and naf's
`STATE.md` (both already project docs; both stay). `STANDING.md`'s absence from every consumer is
**correct, not a gap** (see the correction above): it is nyxloom's own project contract, read by
nyxloom's code (`daemon.py:522`, `decision_chat.py:23`), and was never meant to be stamped.

One more find, surfaced during migration: dstdns's `CLAUDE.md` is **gitignored** — a local-only
file. Shared doctrine living there never reaches another clone at all, which independently
justifies moving the general layer into the tracked, cross-tool `AGENTS.md`.

**Concrete changes** (a carve-able package):

1. Create `reference/` in the nyxloom package; `git mv` AUTHORING/STANDARD out of
   `nyxloom-trove/` (NOT STANDING — see the correction above); add `DOCTRINE.md`.
2. Write `DOCTRINE.md` — promote the operational lessons that today live only in a *consumer's*
   `CLAUDE.md` + session memory: serialize gate runs (one gate container at a time); an agent that
   parks on a backgrounded gate reads as a false-done; trust git state, never receipts; a
   pipe-to-`tail` masks the real exit code. These are orchestration-general — any consumer running
   gates and dispatch hits them.
3. `onboarding.scaffold_trove`: **stop copying doctrine**; stamp the informational `README.md` +
   a fresh `nyxloom.toml` instead.
4. `adapters.py` dispatch: emit a **read-first manifest** naming the canonical docs and their
   trove siblings (mounted per D-R7).
5. Consumers: `CLAUDE.md` → thin `@import AGENTS.md`; `AGENTS.md` points at the product reference
   and names the trove siblings; migrate project-specific prose (dstdns: ciu / pwmcp /
   cockpit-vs-gating / test-runner) into the trove siblings / `GUIDE.md`.
6. Demote `vbpub/nyxloom/nyxloom-trove/` to an ordinary zero-authority trove; nyxloom re-onboards
   itself through the same path — UC4 then validates the mechanism by construction.

**Follow-ons deliberately not folded in:** (a) a trove sibling needs a way to mark itself
*mandatory* reading, so a project can force its environment-critical doc on every agent;
(b) a `nyxloom doctor` check that the running product's doctrine version matches what in-flight
handoffs were authored against, so an upgrade that changes AUTHORING rules surfaces loudly instead
of silently invalidating work.

**Sequencing:** change 4 edits `adapters.py`, which was in in-flight package B21's scope; this
refactor was executed *after* B21 merged. Implementation note: the dispatch manifest is emitted as
a **pointer, budgeted against `argv_max` minus the 200-char headroom** the codebase reserves for
long real paths — so it lands on IMPLEMENTER/CARVER and is skipped for the argv-tight
FRONTIER_REVIEW. A convenience pointer must never be what strands a dispatch.

# P52 - Versioned Daemon Read API

## Goal

Turn the local frame transport into a stable, bounded read API suitable for the
attached TUI and separate frontend processes (web backend, MCP server — see
P58), with explicit versioning, peer identity, and per-client resource bounds.

## Dependency And Workflow

- Starts after reviewed P47 and P51 are merged.
- Branch: `feat/topos-p52-versioned-daemon-read-api`
- Worktree: `.worktrees/-topos-p52-versioned-daemon-read-api`
- Touch only `topos/**`; write P52-LOG.md/P52-REPORT.md; commit, do not merge.

## Context To Read First (bounded — do not survey the whole tree)

`topos/README.md` (Workflow protocol), this handoff, `topos/CONTRACTS.md`,
`src/topos/daemon/` (broker, serve, client, protocol as merged by P47/P51),
their tests under `topos/tests/`, `src/topos/registry.py` (metric metadata),
and `docs/DAEMON.md`. Do not read UI code, DAMON/BPF providers, or unrelated
handoffs; they do not change in this package.

## Required Contracts

### Envelope and negotiation

- Add a versioned request/response envelope: every request carries a client
  `id` (opaque string, echoed verbatim in the response), `op`, and `v`
  (protocol version integer). Every response carries the echoed `id`, `ok`
  boolean, and on failure a typed `error` object (`code` from a closed enum,
  safe `message`) — never a raw exception, secret, filesystem path, or
  arbitrary exception text (the P51 safety contract persists through the new
  envelope; add a test proving it).
- Add a `hello`/negotiate op returning: protocol version(s) served, a
  capability list (closed set of strings naming the ops and optional features
  this daemon build supports), daemon identity/build info safe for local
  disclosure, and current limits (max request bytes, max response items,
  history capacity).
- Pre-P52 clients: define exactly what happens for each existing op
  (`current`, `stream`, `status`, `health-v1`) sent without an envelope —
  either (a) continue to serve it unchanged (compatibility mode), or (b)
  reject with a typed error whose message names the minimum client version.
  Pick ONE behavior per op, document it in `docs/DAEMON.md`, and test both the
  accepted and rejected form. Do not leave the old path silently half-working.
- Strict validation, no silent coercion: reject unknown top-level fields,
  unknown ops, booleans where integers are expected, non-finite numbers,
  negative/zero where a positive bound is required, and malformed cursors —
  each with a distinct typed error code. Constructor/config limits are
  validated the same way: out-of-range values raise; they are never silently
  clamped with `max()`/`min()` (this exact defect survived the optimized-P51
  run — assert on the raising behavior, not just on a valid construction).

### Reads

- Read-only ops: `health` (P47 component health through the new envelope),
  `current` (latest atomic `(sequence, frame)`), `history` (bounded, by
  sequence cursor AND by time window — both forms, each with explicit
  gap/oldest/latest/next-cursor metadata exactly as P51 defined), and one
  `entity` detail op returning a single entity's frame/model data plus
  registry metadata.
- The `entity` op resolves ONLY against daemon-approved frame/model data
  already in memory. No request parameter may reach a filesystem path,
  registry lookup by arbitrary key, command, or sysfs/procfs read. Add a test
  that probes with path-shaped (`../`, absolute, NUL) and registry-shaped
  injection inputs and asserts they produce a typed validation error, not a
  lookup.
- Include registry-derived source/unit/semantic/sensitivity metadata (from
  `src/topos/registry.py`) in `entity` (and where cheap, `current`) responses
  so a web/MCP consumer can render without duplicating registry prose. Define
  the sensitivity levels as a closed enum in `CONTRACTS.md`; every metric in a
  response carries one.

### Peer identity and authorization

- Observe Unix peer credentials (`SO_PEERCRED`: pid/uid/gid) at accept time on
  every connection. Identity is attached to the connection context and appears
  in any audit/rate-limit record produced for that client.
- Add an authorization hook interface (a callable seam, injectable for tests):
  default policy keeps today's socket-group read access, but the hook receives
  (peer credentials, op) and may deny with a typed error. Mutation-shaped ops
  remain rejected unconditionally, before the hook runs.
- Peer-credential read failure (platform or race) is a typed, safe error path:
  define whether the connection is served anonymously or refused, document the
  choice, and test it — do not let an exception escape to the client.

### Resource bounds (enforced, not declared)

- Bound per client: request bytes, request-read time, in-flight requests,
  and response size (items AND total bytes). Bound globally: concurrent
  clients/handler threads. A slow, hostile, or abandoned client must not block
  the producer or other clients (carry the P51 invariants forward through the
  new code paths).
- Every bound must be verified against its actual enforcement mechanism, not
  its constant: the optimized-P51 review found `ThreadingMixIn.max_children`
  silently NOT capping handler threads, and an `rfile` timeout attribute that
  never configured the socket. For each bound, write a test that violates it
  for real (open N+1 concurrent clients and assert the N+1th is refused or
  queued; send a request of exactly the byte cap and one byte over; hold a
  connection idle past the read deadline) and asserts the observable outcome,
  including actual thread counts where the bound is a thread bound.

## Required Deterministic Tests

Events/barriers/bounded polling, not sleeps. Beyond the per-contract tests
above, cover at least: envelope round-trip with id echo; hello capability
completeness (every served op is listed, every listed op is served); each
legacy-op compatibility/rejection decision; malformed/fuzz envelope battery
(unknown field, unknown op, bool-as-int, non-finite, oversized, truncated
line); concurrent mixed clients (one slow + several fast) observing bounded
latency for the fast ones; peer credentials present in audit records; the
sensitivity enum present on every metric of a response; and history
cursor/gap semantics identical through the old (if kept) and new envelope.

Do not weaken or rewrite existing P47/P51 tests to make the new envelope pass;
extend them. Existing daemon attach/status/deployment tests remain green.

## Gates And Evidence

```bash
PYTHONPATH=topos/src python3 -m pytest <focused P52/daemon tests> -q -W error
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error
python3 -m py_compile <all changed/new files>
git diff --check
```

Wrap the broad suite in a `timeout` (the optimized-P51 Pro run hung on an
unbounded pytest command and lost its metrics); if it times out, record that
as an environment/implementation finding in the LOG — never as a pass.
Update `docs/DAEMON.md`, `CONTRACTS.md` (envelope, error codes, sensitivity
enum), readiness/status/roadmap surfaces, and the P52 LOG/REPORT. Do not claim
a gate that was not run; record environment limitations separately from
implementation failures.

## Patch Discipline

Prefer additive modules and focused edits over rewriting existing daemon
files, docs, or tests wholesale — the optimized-P51 run produced an 18-file
+2,295/−305 patch whose reconciliation cost ate much of its quality gain. If
a wholesale replacement genuinely reads better, propose it in the REPORT
instead of committing it.

## Out Of Scope

- HTTP/WebSocket transport, browser auth, TLS/CORS/CSRF, mutation APIs,
  persistent history, frontend framework selection.
- The MCP frontend itself (P58 consumes this API from a separate process).
- Changing collector metric semantics or the registry's metadata content.

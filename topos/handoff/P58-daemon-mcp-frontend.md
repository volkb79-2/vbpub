# P58 - Daemon MCP Frontend

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** sonnet5-high   <!-- escalated 2026-07-13 after the v3 rejection; see the v3 carve note below -->
> **Depends-on:** P52 (merged), P63 (merged)
> **Base:** main after P63 merge
> **Session-hint:** fresh
> **Escalate-if:** a named contract cannot be met as specified; the versioned read API (P52) or its typed client (P63) itself would need changes beyond what P63 delivered

<!--
CARVE NOTE v3 (2026-07-13, frontier pass #2, controller-workflow-v2 §8):
The v3 attempt (branch feat/topos-p58-daemon-mcp-frontend-v3, terra-med) was
REVIEWED AND REJECTED — not merged. Full review: reports/P58-REVIEW-v3.md.

What v3 got RIGHT (keep it — do NOT re-carve, do NOT start over):
  - Consumes the P63 typed DaemonClient exclusively. No raw socket, no envelope
    parsing, no wire JSON in topos.mcp. The architecture violation that killed
    v1 and BLOCKED v2 is genuinely fixed.
  - `topos mcp serve` without the extra exits 2 (v1 blocker, fixed).
  - The four-tool shape and the typed-error taxonomy are the right design.

What must be fixed before re-review (all five are merge blockers):
  B1. MAX_RESPONSE_BYTES is declared and documented but enforced NOWHERE
      (grep: the name appears once, at its own definition). Same blocker the v1
      review rejected. Enforce in _ok(); test it with a payload that violates it.
  B2. Tool descriptions + CONTRACTS.md §11 promise behavior that does not exist:
      a docker name/prefix selector on topos_entity (not implemented), a 4 MiB
      response cap (see B1), a 1000-point history limit (code hardcodes 100 and
      exposes no limit param), and an extended test_textual_boundary.py (the file
      is not touched). For an MCP server the tool description IS the API — the
      calling model reads nothing else.
  B3. _metric_sensitivity() hand-rolls a classifier that topos.daemon.api already
      exports as metric_sensitivity() — from a module server.py ALREADY imports.
      It never returns PUBLIC and misclassifies 46 of 113 registry metrics. Delete
      it; use the canonical function (or the metrics_meta the daemon already
      returns).
  B4. _handle_history hand-rolls a docker name/prefix resolver (the "no third
      resolver" the handoff forbids), while _handle_entity has none — so the two
      tools resolve the same `selector` parameter differently. Pick one.
  B5. No test drives the MCP layer at all: all 27 tests call private _handle_*
      methods. test_tool_discovery_lists_four_tools builds an `expected` set,
      never uses it, and asserts constructor attribute assignment — it passes
      with all four tools deleted. And test_overview_rejects_bool_as_limit
      asserts bool-as-int SUCCEEDS, the opposite of the contract it is named for.

Tier escalated flash-max -> sonnet5-high: the failure mode is no longer code
generation (the plumbing is correct) but specification fidelity — documenting
bounds that do not exist and tests that cannot fail. Prime the re-dispatch with
B1-B5 as the acceptance list.
-->


<!--
CARVE NOTE (2026-07-13, frontier carve authority, controller-workflow-v2 §8):
P58 BLOCKED cleanly twice on the same root cause — P52's `DaemonClient`
(topos/src/topos/daemon/client.py) exposed only legacy-protocol methods and had
NO typed method for the versioned `entity`/`history` envelope ops that
`DaemonApi` serves. flash-max's first attempt hand-rolled a raw socket/envelope
path (rejected as B3 in reports/P58-REVIEW.md); the terra-med v2 retry correctly
refused to repeat that and BLOCKED (reports/P58-REPORT.md on branch
feat/topos-p58-daemon-mcp-frontend-v2). Resolution: carved P63
(topos/handoff/P63-daemon-client-versioned-read-methods.md) to extend
`DaemonClient` with typed, validated `request_current()`/`request_history()`/
`request_entity()`/`request_hello()` methods that own envelope transport,
decoding, and error mapping. Once P63 merges, the "consume P52 exclusively
through the typed adapter" constraint below is satisfiable without any raw
socket/envelope work in `topos.mcp`. Discard both prior P58 branches
(feat/topos-p58-daemon-mcp-frontend and -v2) and dispatch P58 fresh on a base
that includes merged P63; the four MCP tools each route through the P63 client.
-->


## Goal

Add `topos mcp serve`: a read-only Model Context Protocol server exposing the
daemon's frames, health, and entity data as typed MCP tools, so AI CLI agents
(Claude Code, Codex, OpenCode, Reasonix) can query live per-cgroup/container
pressure, memory, and rate data as structured tool calls instead of shelling
out to `topos daemon current | jq`. First consumer: stack-resource-tuning
sessions on gstammtisch (see `scripts/gstammtisch-guide/`), where an agent
asks "which containers are under memory/PSI pressure right now" mid-debug.

## Dependency And Workflow

- Starts ONLY after reviewed P52 AND P63 are merged: P58 consumes the P52
  versioned read API exclusively through the P63 typed `DaemonClient` methods
  (`request_current`/`request_history`/`request_entity`/`request_hello`, plus
  the existing `request_health`). It must not open its own socket protocol
  path, parse wire JSON itself, or duplicate envelope/validation logic — P63
  now provides exactly the typed surface the two prior P58 attempts found
  missing. If the client is STILL missing something P58 needs, propose the
  extension in the REPORT and BLOCK — do not work around it with raw socket
  I/O (that was the B3 rejection; see reports/P58-REVIEW.md).
- Branch: `feat/topos-p58-daemon-mcp-frontend`
- Worktree: `.worktrees/-topos-p58-daemon-mcp-frontend`
- Touch only `topos/**`; write P58-LOG.md/P58-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`topos/README.md` (Workflow protocol), this handoff, `topos/CONTRACTS.md`,
the P52 adapter module and its tests, `src/topos/registry.py` (sensitivity
enum), `docs/DAEMON.md`, and the packaging extras pattern in `pyproject.toml`
(how `textual`/`zstandard` optional extras are declared). Do not read UI,
DAMON/BPF, actions, or record/replay code.

## Required Contracts

### Packaging and process model

- New optional extra `topos[mcp]` depending on the official Python `mcp` SDK
  (pin a minimum version; record the exact version used in the REPORT).
  Importing the MCP SDK (like `textual`) must happen only inside the `mcp`
  subcommand path: `topos --once`, daemon, and all other subcommands work
  with the extra absent, and `topos mcp serve` without the extra exits 2
  with a clear install hint. Add the same structural import-isolation test
  shape P53 specifies for textual (assert no `mcp` module lands in
  `sys.modules` after a non-mcp in-process run).
- Transport v1: stdio only (the standard `claude mcp add topos -- topos mcp
  serve` shape). The process connects to the daemon socket as a normal P52
  client; daemon absent/unreachable at startup is exit-code-nonzero with a
  clear message, and daemon loss mid-session surfaces as typed MCP tool
  errors on subsequent calls, not a crash of the server process.
- Clean shutdown on SIGINT/SIGTERM and on stdin EOF (client disconnect):
  close the daemon client connection, exit 0. Use an injectable
  signal-registration seam (P53's pattern); seams are Python-API-only — the
  production CLI must not grow fixture/test flags (P45's `--fixture-root`
  review lesson).

### Tools (closed v1 set — small on purpose)

1. `topos_health()` → daemon component health (P52 `health` op), verbatim
   typed summary.
2. `topos_overview(sort_by, limit)` → the agent-facing workhorse: top-N
   entities ranked by one of a closed sort-key enum (e.g. `psi_mem_full`,
   `psi_io_full`, `ram`, `rf_z_per_s`), each row carrying entity key, docker
   name when present, and ONLY that compact metric family — not full frames.
3. `topos_entity(selector)` → one entity's detail (P52 `entity` op).
   `selector` accepts an exact `EntityKey` path or a docker container
   name/prefix; name resolution reuses P57's resolver if merged, else exact
   `EntityKey` only (note which in the REPORT; no third resolver
   implementation either way).
4. `topos_history(selector, metric, window)` → bounded time series for ONE
   metric of ONE entity (list of `(ts, value)`), for "did this spike"
   questions.

No tool executes actions, reads files, or accepts paths/registry keys that
reach beyond P52's already-validated surface. Mutation-shaped requests are
structurally impossible (no such tool), not merely refused.

### Response bounds (token budget is the constraint)

MCP tool results land in an LLM context window; a full frame is ~447 KB
(P53 amendment) and would destroy the calling agent's session. Therefore:
- Every tool has an explicit item limit (validated, capped maximum — reject
  over-limit requests with a typed error, never silently clamp) and an
  aggregate response-byte cap; state both caps in the tool descriptions so
  the calling model can plan.
- `None`-valued metrics are omitted, not serialized as nulls.
- Tool descriptions (the text the LLM reads) are part of the deliverable:
  one sentence of purpose, the units/semantics source (registry), and the
  bound. Review them for token cost like code.

### Sensitivity and error policy

- Every metric value returned carries or respects the P52 registry
  sensitivity enum. Add a `--redact-above LEVEL` server flag (default: no
  redaction — local admin tool); redaction replaces the value with a typed
  marker, never drops the key silently.
- No raw daemon/adapter exception text, socket paths, or environment detail
  crosses into an MCP result: map every adapter failure to a closed set of
  typed MCP tool errors (daemon-unavailable, invalid-selector, over-limit,
  internal). Add the P51-style leak test: a fake adapter raising
  `RuntimeError("TOKEN=topsecret /private/path")` must produce a tool error
  containing neither substring.

## Required Deterministic Tests

Drive the real MCP server in-process with an MCP client from the SDK against
a fake/injected P52 adapter (no live daemon, no real socket in the default
suite): tool discovery lists exactly the four tools; each tool's happy path;
strict argument validation (unknown sort key, zero/negative/over-cap limits,
bool-as-int) each yielding a typed error; the leak test above;
daemon-loss-mid-session behavior; overview ranking correctness on a fixture
frame with known values (assert the exact expected order and that response
bytes stay under the cap with a maximal fixture); import isolation; and the
signal/EOF shutdown path via the seam. Assert observable MCP-level results,
not internal call counts alone.

## Gates And Evidence

```bash
PYTHONPATH=topos/src python3 -m pytest <focused P58 tests> -q -W error
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error
python3 -m py_compile <all changed/new files>
git diff --check
```

State explicitly in the REPORT which environment each result came from; a
live end-to-end check against a real daemon is controller-side evidence, not
an agent claim. Update `README.md` (quickstart: the `claude mcp add` line),
`docs/DAEMON.md` (frontends section), `CONTRACTS.md` (tool set + bounds),
`docs/ROADMAP.md`/`docs/STATUS.md`.

## Out Of Scope

- HTTP/SSE/streamable transport, network exposure, auth (stdio + Unix
  socket permissions are the v1 trust boundary).
- Any mutation/action tools, squeeze integration, DAMON control.
- Prometheus/OpenTelemetry export (different consumers, different package).
- Frame-schema or daemon protocol changes (P52 owns the wire).

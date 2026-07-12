# P58 - Daemon MCP Frontend

## Goal

Add `groop mcp serve`: a read-only Model Context Protocol server exposing the
daemon's frames, health, and entity data as typed MCP tools, so AI CLI agents
(Claude Code, Codex, OpenCode, Reasonix) can query live per-cgroup/container
pressure, memory, and rate data as structured tool calls instead of shelling
out to `groop daemon current | jq`. First consumer: stack-resource-tuning
sessions on gstammtisch (see `scripts/gstammtisch-guide/`), where an agent
asks "which containers are under memory/PSI pressure right now" mid-debug.

## Dependency And Workflow

- Starts ONLY after reviewed P52 is merged: P58 consumes the P52 versioned
  read API exclusively through P52's typed Python adapter. It must not open
  its own socket protocol path, parse wire JSON itself, or duplicate
  envelope/validation logic. If the adapter is missing something P58 needs,
  propose the adapter extension in the REPORT — do not work around it.
- Branch: `feat/groop-p58-daemon-mcp-frontend`
- Worktree: `.worktrees/-groop-p58-daemon-mcp-frontend`
- Touch only `groop/**`; write P58-LOG.md/P58-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`groop/README.md` (Workflow protocol), this handoff, `groop/CONTRACTS.md`,
the P52 adapter module and its tests, `src/groop/registry.py` (sensitivity
enum), `docs/DAEMON.md`, and the packaging extras pattern in `pyproject.toml`
(how `textual`/`zstandard` optional extras are declared). Do not read UI,
DAMON/BPF, actions, or record/replay code.

## Required Contracts

### Packaging and process model

- New optional extra `groop[mcp]` depending on the official Python `mcp` SDK
  (pin a minimum version; record the exact version used in the REPORT).
  Importing the MCP SDK (like `textual`) must happen only inside the `mcp`
  subcommand path: `groop --once`, daemon, and all other subcommands work
  with the extra absent, and `groop mcp serve` without the extra exits 2
  with a clear install hint. Add the same structural import-isolation test
  shape P53 specifies for textual (assert no `mcp` module lands in
  `sys.modules` after a non-mcp in-process run).
- Transport v1: stdio only (the standard `claude mcp add groop -- groop mcp
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

1. `groop_health()` → daemon component health (P52 `health` op), verbatim
   typed summary.
2. `groop_overview(sort_by, limit)` → the agent-facing workhorse: top-N
   entities ranked by one of a closed sort-key enum (e.g. `psi_mem_full`,
   `psi_io_full`, `ram`, `rf_z_per_s`), each row carrying entity key, docker
   name when present, and ONLY that compact metric family — not full frames.
3. `groop_entity(selector)` → one entity's detail (P52 `entity` op).
   `selector` accepts an exact `EntityKey` path or a docker container
   name/prefix; name resolution reuses P57's resolver if merged, else exact
   `EntityKey` only (note which in the REPORT; no third resolver
   implementation either way).
4. `groop_history(selector, metric, window)` → bounded time series for ONE
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
PYTHONPATH=groop/src python3 -m pytest <focused P58 tests> -q -W error
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error
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

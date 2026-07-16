# P75 - MCP Live-Daemon Acceptance Leg

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P58 (merged), P52 (merged), P63 (merged)
> **Base:** main after P58 merge
> **Session-hint:** fresh
> **Serialize-with:** none
> **Escalate-if:** a named contract cannot be met as specified; closing the gap would require changing `topos.mcp.server` behavior rather than exercising it

<!--
CARVE NOTE (2026-07-13, frontier pass #2 on P58 v4, controller-workflow-v2 §8):
Carve source: REVIEW-DERIVED (source 1). P58 merged with an evidence gap its own
REPORT states plainly: "A live daemon end-to-end session was not claimed; it
remains controller-side evidence as required." Every P58 test drives an injected
fake client -- correct for a deterministic suite, but it means no automated check
has ever run `topos mcp serve` against a real daemon over a real socket. The
optional-extra packaging bug the P58 review had to fix (the suite aborted at
collection without `topos[mcp]`) is exactly the class that a fake-client-only
suite cannot see. This package closes that with the harness pattern topos already
uses three times (P33 smoke, P35 steady, P38 tui-smoke), not with a new one.
-->

## Goal

Add an `mcp-smoke` leg to `python -m topos.acceptance` that starts a real daemon,
runs a real `topos mcp serve` process, drives all four MCP tools through a real
MCP client over stdio, and emits the same JSON/text evidence shape the existing
legs emit. This converts P58's live end-to-end check from a controller-side manual
claim into a rootless, repeatable, committed harness.

## Dependency And Workflow

- Starts after merged P58. Consumes `topos.mcp.server` and `topos.daemon` as they
  are; this package **exercises** the frontend, it does not change it. If a
  contract below cannot be met without editing `topos/src/topos/mcp/server.py`,
  that is a finding for the REPORT and a BLOCKED exit -- not a silent edit.
- Branch: `feat/topos-p75-mcp-live-acceptance-leg`
- Worktree: `.worktrees/topos-p75-mcp-live-acceptance-leg`
- Touch only `topos/**`; write P75-LOG.md/P75-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`topos/README.md` (Workflow protocol), this handoff,
`topos/src/topos/acceptance.py` (the three existing legs -- `run_smoke`,
`run_steady`, `run_tui_smoke`, and their `format_*_json`/`format_*_text` pairs and
`build_parser` wiring are the exemplar to copy), `topos/src/topos/mcp/server.py`,
`topos/src/topos/daemon/client.py`, `topos/handoff/reports/P58-REVIEW.md`,
`topos/docs/DAEMON.md` (MCP frontend section). Do not read UI, DAMON/BPF, actions,
or record/replay code.

## Required Contracts

### The leg

- `python -m topos.acceptance mcp-smoke` follows the existing leg shape exactly:
  a `run_mcp_smoke()` returning a result dataclass, `format_mcp_smoke_json()` /
  `format_mcp_smoke_text()`, and a `build_parser()` subparser with the same
  `--json`/`--pretty` flags the sibling legs take. Do not invent a new CLI shape.
- **Rootless.** Like P33/P35/P38 it must run as an unprivileged user in CI. It
  starts its own daemon against a temp socket path in a temp dir -- it must not
  require, assume, or touch the packaged system socket, and must not need root.
- **Self-contained lifecycle.** The leg starts the daemon, waits for the socket to
  accept a connection (bounded wait with a timeout, no unbounded spin, no bare
  `sleep` as the synchronization primitive), runs the checks, then tears both
  processes down. On any failure path -- including timeout and assertion failure --
  the daemon and the server process are still terminated. A leaked daemon process
  is a failing outcome, not an acceptable one.
- **Skips honestly when it cannot run.** If the `mcp` extra is absent the leg
  reports a typed `skipped` check with the reason and exits 0; it must not fail,
  and must not silently report success. "MCP extra absent" and "MCP tools all
  passed" must be distinguishable in both the JSON and the text output. (This is
  the P71/P74 "no GPU must not render like a GPU I cannot read" rule applied here.)

### The checks (each a `Check` in the existing sense: name, ok, detail)

1. `topos mcp serve` connects to the live daemon and completes the `hello` probe.
2. Tool discovery over a real MCP client session lists exactly the four tools
   `topos_health`, `topos_overview`, `topos_entity`, `topos_history`.
3. Each of the four tools returns a well-formed successful result against the live
   daemon, with `topos_entity`/`topos_history` driven by an entity key **taken from
   the live `topos_overview` response**, not a hardcoded fixture key -- the point of
   this leg is that the real daemon's real keys round-trip.
4. Every response is under the 4 MiB cap, and the observed byte size of the largest
   response is **recorded as a number in the evidence output**, not merely asserted.
   The recorded figure is the deliverable: it is what tells us whether the cap has
   real headroom on a live host or is one busy container away from tripping.
5. Daemon loss mid-session surfaces as a typed `daemon-unavailable` tool result and
   **not** a crash of the server process: kill the daemon, call a tool again, assert
   the typed error and that the server process is still alive.
6. A bogus selector yields the typed `invalid-selector` error against the live
   daemon (proving the P57 resolver path works on real docker metadata, which no
   fake-client test can show).

### Evidence integrity

- The leg reports what it observed. It must not degrade a failed check into a skip,
  and it must not report `ok` for a check it did not actually execute. Every `ok`
  in the output corresponds to an assertion that ran against the live processes.

## Required Deterministic Tests

The leg itself is a live harness, so the *unit* tests around it must not require a
live daemon:
- `format_mcp_smoke_json`/`_text` render a known result dataclass deterministically
  (byte-for-byte on a fixture), including the extra-absent skip shape and a mixed
  pass/fail shape.
- `build_parser` wires `mcp-smoke` with the same flags as the sibling legs.
- The teardown contract is tested: a check that raises mid-run still terminates both
  child processes (inject the process handles; assert terminate/kill was called).
  This is the contract most likely to be silently broken, so it gets a real test that
  fails if the `finally` path is removed.
- The extra-absent path yields `skipped`, exit 0, and is textually distinguishable
  from success.

Gate the live leg itself behind the existing acceptance-harness convention (it is
run explicitly, not part of the default `pytest topos/tests` sweep). Do not add a
test to the default suite that starts a daemon.

## Gates And Evidence

```bash
PYTHONPATH=topos/src python3 -m pytest <focused P75 tests> -q -W error
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error
python3 -m py_compile <all changed/new files>
git diff --check
```

Use the package venv (`/usr/local/py-utils/venvs/pytest/bin/python`) if bare
`python3` trips an unrelated `-W error` deprecation at import; state in the REPORT
which interpreter produced each result.

Additionally, **run the new leg** and paste its real output into the REPORT:

```bash
PYTHONPATH=topos/src python3 -m topos.acceptance mcp-smoke --json --pretty
```

State explicitly whether the `mcp` extra was installed in that environment and
whether a real daemon was reachable. If the leg could only be run in skip mode,
say so plainly -- an honest skip is a fine REPORT; a fabricated pass is not.

Update `docs/DAEMON.md` (MCP frontend section: how to run the leg),
`docs/RELEASE-READINESS.md` (the ledger of what is proven and how), `CONTRACTS.md`
§11 if and only if a bound changes (it should not), `docs/ROADMAP.md`,
`docs/STATUS.md`.

## Out Of Scope

- Any change to `topos/src/topos/mcp/server.py` behavior, tool set, or bounds.
- Adding MCP tools, transports (HTTP/SSE), or auth.
- Making the live leg part of the default pytest sweep.
- Changing the daemon, the P52 wire, or the P63 client.

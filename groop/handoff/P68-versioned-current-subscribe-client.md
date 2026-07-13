# P68 - Versioned Current Subscribe (Server Op + Typed Client)

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** pro-high
> **Depends-on:** P63 (merged — typed client read surface), P52 (merged — versioned envelope), P51 (daemon sampling/fanout, merged)
> **Base:** main
> **Session-hint:** fresh
> **Escalate-if:** adding a live subscribe op to the versioned envelope would fork or contradict the legacy `stream_frames`/`stream_batch` streaming contract rather than layering cleanly beside it; or the broker cannot deliver an incremental, back-pressure-safe frame feed without changing sampling/fanout semantics (P51 owns those) — in either case escalate rather than reshaping the broker or the legacy stream.

## Goal

P63 gave frontends typed one-shot versioned reads, but a live MCP/web consumer
that negotiated the versioned envelope still has to drop to the *legacy*
`stream_frames` protocol for continuous updates. Add a versioned live feed:
a server-side subscribe/tail op on the P52 envelope that streams successive
`current`-shaped frames (bounded, back-pressure-safe), plus a typed
`DaemonClient.stream_current_versioned()` generator mirroring the legacy
`stream_frames` pattern (bounded reads, `DaemonProtocolError` on malformed
lines, monotonic-seq checks). This is a **server + client** package because the
versioned envelope currently has no streaming op — coordinate carefully with
the existing legacy stream so both coexist.

## Dependency And Workflow

- This DOES extend `api.py` (a new versioned op) — unlike P63, which was
  read-only client work. Keep the new op strictly additive: existing ops
  (`hello`/`current`/`history`/`entity`/`health`) and the legacy streaming
  protocol keep exact behavior. Advertise the new capability in `_op_hello`'s
  `capabilities` so `request_hello()` consumers can feature-detect it.
- Reuse P51's broker fanout for delivery; do not invent a second frame source.
- The typed client method mirrors legacy `stream_frames`: bounded per-line
  read, id/seq validation, clean generator close on socket teardown.
- Branch: `feat/groop-p68-versioned-current-subscribe-client`
- Worktree: `.worktrees/groop-p68-versioned-current-subscribe-client`
- Touch only `groop/**`; write P68-LOG.md/P68-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`groop/README.md`, this handoff, `groop/src/groop/daemon/api.py` (the op
dispatch + `_op_hello` capabilities + how `_op_history` uses the broker),
`groop/src/groop/daemon/client.py` (P63 `_request_envelope` + legacy
`stream_frames`/`stream_batch` — mirror the streaming discipline),
`groop/src/groop/daemon/broker.py`/fanout (P51), and `docs/DAEMON.md`. Do not
read UI, actions, or record/replay code.

## Required Contracts

- A new versioned subscribe op with a documented framing: one request opens a
  bounded stream of `current`-shaped lines each with monotonic `seq`; server
  enforces a max in-flight / drops-with-gap-marker policy consistent with
  existing history `gap` semantics rather than growing memory unboundedly.
- `stream_current_versioned()` typed generator on `DaemonClient`, bounded reads,
  `DaemonProtocolError` on malformed/oversized/non-monotonic lines,
  `DaemonResponseError` (with `.code`) on an `ok:false` opener.
- Legacy `stream_frames`/`stream_batch` and all P63 methods untouched.
- Capability advertised in `hello`; typed result reuses P63's `Frame` decoding
  and `metrics_meta` validation.

## Required Deterministic Tests

Drive the real `DaemonApi` + broker in-process over a temp `AF_UNIX` socket;
events/barriers/bounded polling, not sleeps. Cover: subscribe yields N known
frames in monotonic-seq order then closes cleanly; a slow consumer triggers the
documented bounded/gap policy (assert the gap marker, no unbounded buffer);
malformed/oversized line raises `DaemonProtocolError`; `hello` advertises the
new capability; legacy stream tests stay green unchanged.

## Gates And Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P68 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result; record `-p no:schemathesis`/textual
skips as known artifacts. Update `docs/DAEMON.md`, `CONTRACTS.md` (if it
enumerates envelope ops/capabilities), and the P68 LOG/REPORT.

## Out Of Scope

- Write/mutation ops; HTTP/WebSocket transport (P67 owns network transport).
- Changing sampling/fanout cadence (P51) or history/current result shapes.
- The MCP frontend itself (P58 consumes this).

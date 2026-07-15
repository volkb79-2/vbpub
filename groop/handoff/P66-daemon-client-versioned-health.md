# P66 - Daemon Client Versioned Health Method

<!-- controller-workflow-v2 header: parsed by the controller; see docs/controller-workflow-v2.md §7 -->
> **Tier:** flash-high
> **Depends-on:** P63 (merged, reviewed), P52 (merged), P47 (component health, merged)
> **Base:** main (dependency-complete `[dev]` gate)
> **Session-hint:** fresh
> **Escalate-if:** the P52 `DaemonApi._op_health` result shape cannot be expressed as a frozen typed result without touching `api.py`; or completing the versioned health method would require changing any legacy method (`request_health` legacy-protocol path must stay byte-for-byte identical).

## Goal

Complete the typed versioned-envelope read surface P63 built on
`DaemonClient` by adding the one op P63 left out: `health`. P63 added typed
methods for `hello`/`current`/`history`/`entity` but the versioned `_op_health`
(P52 `api.py`) still has no typed client method — only the *legacy-protocol*
`request_health` exists. A frontend that negotiated the versioned envelope via
`request_hello()` should be able to read component health through the same
envelope + `_request_envelope` transport, not fall back to the legacy socket
protocol.

## Dependency And Workflow

- Additive client-surface work on merged P63. Mirror P63's exact pattern:
  reuse `_request_envelope`, add a frozen `DaemonVersionedHealthResult` (name
  your call; do not collide with the legacy `HealthSnapshot`), one typed method.
- Branch: `feat/groop-p66-daemon-client-versioned-health`
- Worktree: `.worktrees/groop-p66-daemon-client-versioned-health`
- Touch only `groop/**`; write P66-LOG.md/P66-REPORT.md; commit, do not merge.

## Context To Read First (bounded)

`groop/README.md`, this handoff, `groop/src/groop/daemon/client.py` (P63's
`_request_envelope`, `request_current`, and result dataclasses — mirror them),
`groop/src/groop/daemon/api.py` (`_op_health` only — the result shape you
decode), `groop/src/groop/daemon/component_health.py` (existing health models
and `HealthSnapshot` — decode into the established model where it already fits,
do not re-derive), and `groop/tests/test_daemon_client_p63.py` for the test
harness shape. Do not read UI, DAMON/BPF, actions, or record/replay code.

## Required Contracts

- Add `request_health_versioned()` (or a clearly-named non-colliding method)
  returning a frozen dataclass that carries the `_op_health` result decoded and
  validated. Reuse `_request_envelope("health")`; do NOT add a second transport.
- Decode component states into the existing `component_health` models where the
  shape matches; validate defensively (unknown component names / missing fields
  raise `DaemonProtocolError`, mirroring P63's per-field validation).
- The legacy `request_health` and every other legacy/P63 method keep exact
  current behavior and signatures. Purely additive.
- Export the new result type through `groop/src/groop/daemon/__init__.py`.

## Required Deterministic Tests

Drive the real `DaemonApi` health envelope in-process over a real/`socketpair`/
temp-`AF_UNIX` transport (same harness as `test_daemon_client_p63.py`), not a
hand-mocked socket. Cover: happy path decodes a known component state + overall
status; an `ok:false` health envelope surfaces `DaemonResponseError` with
`.code`; malformed/oversized/non-object response raises `DaemonProtocolError`;
`id` echo asserted. Do not weaken existing P63/P52/health tests; extend.

## Gates And Evidence

```bash
PYTHONPATH=groop/src python3 -m pytest <focused P66 tests> -q -W error -p no:schemathesis
timeout 900 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q -W error -p no:schemathesis
python3 -m py_compile <changed files>
git diff --check
```

State the environment for each result. Build the P84 `[dev]` environment; the
authoritative full gate permits zero skips. Update `docs/DAEMON.md` (client
section) and the P66 LOG/REPORT.

## Out Of Scope

- Any change to the P52 wire/envelope/error codes/sensitivity enum, or to
  `api.py`. This consumes the existing `_op_health` only.
- Streaming/subscribe health; the MCP frontend; write/mutation ops.
- Changing component-health semantics (P47 owns those).

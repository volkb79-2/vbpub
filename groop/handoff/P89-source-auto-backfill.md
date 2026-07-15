# P89 - Visible source auto-selection and daemon backfill

<!-- controller-workflow-v2 header: parsed by the controller -->
> **Tier:** sonnet5-high
> **Depends-on:** P88
> **Base:** main after P88
> **Session-hint:** resume P88 if warm
> **Serialize-with:** none
> **Escalate-if:** fallback would require hiding a privilege/provenance change, or backfill cannot reuse P88 without a second history cache.

## Goal

Implement D-004's zero-argument source behavior: prefer a compatible daemon,
backfill its recent bounded history immediately, and visibly fall back to local
collection when it is absent or incompatible.

## Required contracts

1. Strict config supports `source = "auto"|"daemon"|"local"`, socket path,
   discovery order, connect/request timeouts and bounded retry/backoff. Defaults
   are documented; invalid combinations fail startup.
2. `auto` probes the configured daemon once with the typed hello/health client.
   On success it labels all output `DAEMON`; on failure it starts local
   collection labelled `LOCAL-DEGRADED` with a concise reason and remediation.
   Explicit `daemon` remains fail-closed and explicit `local` does not probe.
3. Attach fetches the available five-minute fast-tier window through P88 before
   live polling. UI/query status reports observed coverage, gap/eviction,
   backfill state and freshness. No blank five-minute warm-up.
4. Source changes are explicit events. Never splice daemon and local counters
   across a rate baseline; mark the reset/source boundary.
5. Status/CLI/TUI consume the same source state object. No SSH handling, port
   forwarding, discovery broadcast or silent privilege escalation belongs here.

## Acceptance oracles

Use real temporary daemon sockets and fake local collectors to prove daemon
preference, absent/incompatible fallback, explicit-daemon refusal, no probe in
local mode, immediate bounded backfill, source-change reset, timeout/backoff and
operator-visible labels. A disabled label assertion must make the suite fail.

## Out of scope

Persistent storage (P91), web auth/serving (P92), SSH setup, live push and
provider activation leases.

## Gates

Focused daemon/client/CLI/TUI tests, zero-skip full suite, compile checks and
`git diff --check`. Write P89-LOG.md and P89-REPORT.md.

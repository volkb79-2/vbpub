---
kind: backlog-entry
schema_version: 1
id: CP-15
title: "daemon has no reservation verb: a consumer-decided admission (ciu v8) cannot record a start's reserved memory before a container exists"
status: open
type: "feature"
severity: "medium"
provenance: "dstdns D-651 Q4 / D-652 Q6; ciu v8 proposal rev 4.0 vbpub@177309c32 V8-31"
filed_date: "2026-10-03"
---

## Observed mechanism
The ciu v8 proposal revision 4.0 (vbpub@177309c32, §4.1.10a, Appendix R row V8-31) makes ciu the admission decider: it decides under a host lock and registers each admitted start's reservation with the cgprofile daemon as data (dstdns D-651 Q4, D-652 Q6). The daemon has no verb that can hold such a reservation:
- Contract 1 / v1.1 (`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`) offers only profiling sessions: `start` / `status` / `stop` / `watch`.
- A session needs a running container to attach to, so a stack that has not started yet cannot be represented.
- The daemon holds at most 16 sessions.
- It matches the literal variable `RUN_GATE_PROFILE_SESSION`, so any non-run-gate consumer has to impersonate run-gate's name.

## Why cgprofile owns it
The daemon is the only host-wide vantage that sees slice usage across devcontainers and projects. A reservation is data in that view. The decision itself stays with the consumer (ciu); the daemon only records and reports.

## Proposed contract
- `ctl reserve --owner <consumer-id> --bytes N --ttl S [--slice S] --json`: returns a reservation id. It requires no running container.
- `ctl release <id>`. TTL expiry releases automatically.
- `ctl reservations --json`: lists live reservations, with owner, bytes, slice and age.
- `ctl host` includes the sum of live reservations per slice next to the measured usage.
- The session-matching variable gets a consumer-neutral name (e.g. `CGPROFILE_SESSION`), with run-gate's name kept as an alias for one release only if needed. Prefer a clean rename coordinated with run-gate.
- The daemon never refuses or admits anything; the consumer decides.

## Oracles
- A reservation made with no container shows in `ctl host` totals.
- Release, and separately TTL expiry, remove it from the totals.
- Two concurrent reserves from different owners are both recorded.
- A controlled wrong implementation that computes `host` totals without reservations fails the first oracle.

## SPEC owner
RG55-INTERFACE-CONTRACT (contract 1 → the next additive version); cgprofile SPEC daemon section.

## Updates

**2026-10-03** — Downgraded by dstdns D-655 (2026-10-03): v8 Q15 chose Docker-object reservations as the serialization medium, so ciu never registers reservations with the daemon. CP-15 is now optional, a read-only mirror of reservations for observability (ctl host totals). Not required for admission.

**2026-10-03** — Scope (dstdns D-658, 2026-10-03): ciu's admission is **v8.1** (SPEC-V8 S21, one switch, default off). Reservations are name tickets on Docker containers (ciu-res-<tier>-<n>), ordered gap-free by Docker's atomic container-name conflict, and the daemon takes no part; this entry stays an OPTIONAL observability mirror, v8.1-scoped, and nothing in 8.0 or v8.1 requires it.

**2026-10-03** — Review against SPEC-V8 draft.11 (dstdns D-661, D-666): ciu's admission is now split — the **count mode is 8.0** (Docker-name tickets `ciu-res-gates-<n>` on the `gates` tier, the published object `ciu-admission-<g>`, `ciu host admission set|show`, behind `[admission] enabled`, default `false`; S21) and the **byte budget is v8.1**. Neither registers anything with the daemon: tickets are Docker objects ordered by the atomic name conflict, and the daemon measures and decides nothing (S21.4.1). This entry therefore stays an OPTIONAL read-only mirror for observability, now able to cover both modes: `ctl reservations` could list the live `ciu-res-*` tickets, read from Docker and never written by the daemon, and `ctl host` could show their count next to measured usage. The proposed `--bytes N --ttl S` reserve verb is meaningful only as a mirror of v8.1's charge. The switch is `[admission] enabled` (it replaced `[ciu] admission`, D-661). Run-gate's v7 build of the same tickets is RG-80 (proposal N28). Nothing in 8.0, v8.1 or v8.2 (remote deployment) requires this entry.

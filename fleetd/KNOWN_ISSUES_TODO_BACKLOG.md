# fleetd known issues and TODO backlog

## Summary

| ID | Item | Priority | State | Filed |
|---|---|---|---|---|
| FD-001 | Fleet-level campaign submission, supervision, collection, and strict Assay merge are not implemented | P1 | OPEN | 2026-11-13 |
| FD-002 | Standalone lane demand does not derive Assay mutation.jobs_max | P1 | OPEN | 2026-11-13 |
| FD-003 | Manual Run-Gate commands cannot pass fleetd's per-host RAM budget to Assay | P1 | OPEN | 2026-11-13 |

## FD-001 Fleet-level campaign lifecycle is not implemented

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

This MVP validates inputs, collects local host facts, and prints plans and
candidate-selection commands. It does not submit campaigns, supervise named
jobs, collect remote stores, re-plan from progress, merge results, or maintain
the campaign ledger. Implement the design's daemon and transport work packages
only after the required host-entry, Run-Gate, Assay, and enrollment interfaces
are accepted. No daemon, socket, SSH, or Docker behavior is implied by the
current CLI.

## FD-002 Demand upper bound is not joined to Assay inventory

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

WP2 accepts `[lanes.<lane>.demand].efficient_upper_bound` from a standalone
TOML file because Run-Gate 25.6.0 does not accept the `demand` key in
`run-gate.toml`. The same value must be no greater than the lane's authoritative
Assay `judge.mutation.jobs_max`; the current `fleet plan` CLI cannot derive that
inventory and therefore cannot reject a stale or oversized declaration. Until
the Run-Gate campaign client owns demand parsing, operators must keep these
values aligned. The follow-on client must derive the upper bound from Assay or
refuse a mismatch before rendering an executable command.

## FD-003 Fleet RAM budget is not sent to the current runner

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

`fleet plan` computes `B_h` and proves each planned wave against it, but the
current Run-Gate `--only-candidates` interface has no per-invocation
host-specific Assay memory-budget argument. The printed command supplies the
planned worker count only; it does not enforce `B_h`. Runtime admission remains
owned by the host's existing Run-Gate, Assay lane, and cgroup configuration.
The Run-Gate campaign client must carry the selected host budget into Assay's
memory admission (or establish an equivalent enforced bound) before treating
the plan's wave proof as an execution guarantee.

# fleetd known issues and TODO backlog

## Summary

| ID | Item | Priority | State | Filed |
|---|---|---|---|---|
| FD-001 | Fleet-level campaign submission, supervision, collection, and strict Assay merge are not implemented | P1 | OPEN | 2026-11-13 |

## FD-001 — Fleet-level campaign lifecycle is not implemented

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

This MVP validates inputs, collects local host facts, and prints plans and
candidate-selection commands. It does not submit campaigns, supervise named
jobs, collect remote stores, re-plan from progress, merge results, or maintain
the campaign ledger. Implement the design's daemon and transport work packages
only after the required host-entry, Run-Gate, Assay, and enrollment interfaces
are accepted. No daemon, socket, SSH, or Docker behavior is implied by the
current CLI.

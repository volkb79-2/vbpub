---
kind: backlog-entry
schema_version: 1
id: NL-53
title: "wrapper leaves the spawned child unsupervised when publishing child.pid fails"
status: fixed
type: "bugfix"
severity: "low"
component: "wrapper"
provenance: "independent review of the atomic child.pid change, 2026-11-01"
filed_date: "2026-11-01"
closed_date: "2026-11-03"
closed_reason: "FIXED 2026-11-04 (55792dfe6)"
---

## Observed mechanism

`src/nyxloom/wrapper.py` (around line 530) spawns the child and then publishes
`child.pid` through a sibling temp file and `os.replace`. If that write raises
(ENOSPC, read-only filesystem), the child keeps running with nothing
supervising it, because the `finally` block only restores signal handlers. A
`child.pid.tmp` may remain; it is harmless since the only reader opens
`child.pid` by exact name.

## Expected

When pid publication fails, the wrapper terminates the child's process group
(TERM, then KILL after the grace period), removes the temp file, and records a
blocked receipt instead of orphaning the child. Add a test that forces the
write to fail and asserts the child is gone.

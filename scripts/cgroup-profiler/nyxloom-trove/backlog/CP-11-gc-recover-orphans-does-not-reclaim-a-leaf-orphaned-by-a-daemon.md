---
kind: backlog-entry
schema_version: 1
id: CP-11
title: "gc/_recover_orphans does not reclaim a leaf orphaned by a daemon restart -- a CP-9 placement leaf outlives the process that placed it"
status: open
type: "bugfix"
severity: "low"
provenance: "RG-55 wave, cgprofile-P6-FOLLOWUPS session 6 C8 REPORT + RW-44, filed session 7, 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism

CP-9 (C8, `a654bd5d`) placed a lane's pids under a leaf cgroup
(`<gates slice>/rg-<token>`) and, on `stop`, moves survivors back and
`rmdir`s the leaf. If the DAEMON PROCESS ITSELF dies (crash, host reboot,
`ciu down`) while a session is placed and live, the leaf is never released
through that path — the daemon simply stops running.

`_recover_orphans` (the daemon's own startup recovery for sessions whose
`run` directory says `live` but whose process is gone) finalizes the
session's manifest so a consumer sees a terminated session rather than a
permanently "live" one. It has no `LanePlacement` object to call `release`
on (that object lived in the dead process's memory, not on disk), so it
never migrates survivors back or `rmdir`s the leaf. The leaf — and whatever
pids are still in it — is left behind under the gates slice with no code
path that will ever clean it up.

The P6 round-3 safety repair refuses a later placement request when that
token's leaf already exists. It does not reclaim the old leaf or its pids;
this row remains open. Refusal prevents a new session from silently taking
ownership of the orphan and changing its caps or killing its processes.

## Why this matters

A gates slice accumulating orphaned `rg-<token>` leaves after every daemon
restart during development (exactly the pattern this wave's own sessions
hit repeatedly — worktree rebuilds, `ciu down`/`up` cycles) slowly starves
the slice's own capacity accounting and leaves stray pids attributed to a
session an operator can no longer query through `ctl`.

## Proposed direction (not implemented, not spec'd — CP-1's retention
sweep is the natural owner)

`_recover_orphans` (or the retention sweep CP-1 already owns) could, for
each `<gates slice>/rg-*` leaf that has no live session claiming it: read
`cgroup.procs`, move survivors back to the gates slice root (or kill them —
policy TBD), then `rmdir`. This needs its own decision on WHERE survivors
go when the session's original scope is also gone (the container that
lane belonged to may itself no longer exist), which §8.3 does not specify
and this row deliberately does not invent.

## Provenance

RG-55 wave, cgprofile-P6-FOLLOWUPS session 6 (C8/CP-9 REPORT, "Deferred out
of C8"), flagged for filing by RW-44; filed by session 7, 2026-09-12.
